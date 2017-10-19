from __future__ import print_function, division

import os,json,sys,argparse,socket
import tempfile,shutil,math,random,http.server
import requests

### bbox
## Find the bounding box
def findext(server,dim,tiles,maxlevel,func,initp,numoftiles=5):
    query_url='%s/count.a("%s",dive(tile2d({x},{y},{z}),{drill}),"img")'%(server,dim)
    
    p = initp
    newtiles=[]
    for location in tiles:
        res =  requests.get(query_url.format(**location),
                            verify=False).json()
        #res_str = res_str.replace('\x00','}') # this is bad
        #res = json.loads(res_str)

        res = res['root']['children']
        offset = {'x':location['x']*2**location['drill'],
                  'y':location['y']*2**location['drill'],
                  'z':location['z']+location['drill']}

        if 'path' in res[0]:  #if the result is path and not x y ... (lauro...)
            res =  [ {'x':r['path'][0], 'y':r['path'][1], 'val':r['val']} 
                     for r in res]
  
        #add offset
        res = [{'x':d['x']+offset['x'],
                'y':d['y']+offset['y'],
                'z':offset['z']} for d in res]
        
        #find extrememum
        p={'x':func(p['x'],func(res,key=lambda d: d['x'])['x']),
           'y':func(p['y'],func(res,key=lambda d: d['y'])['y']),
           'z':offset['z']}

        newtiles += [ t for t in res \
                      if  (t['x'] == p['x'] or t['y'] == p['y'])]

    if p['z'] >= maxlevel: # base case
        return p

    #filter out excessive tiles
    newtiles = [ t for t in newtiles \
                 if  (t['x'] == p['x'] or t['y'] == p['y'])]

    random.shuffle(newtiles)

    #Go into the next level
    nextdrill = min(8,maxlevel-p['z'])
    for t in newtiles: t['drill'] = nextdrill

    return findext(server,dim,newtiles[:numoftiles],maxlevel,
                   func,initp,numoftiles)

def num2deg(p):
    xtile = p['x']
    ytile = p['y']
    zoom = p['z']
    
    n = 2.0 ** zoom
    ytile = n-ytile-1

    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return [lat_deg, lon_deg]

def findbox(server,dim,maxlevel):
    # min query
    minp = findext(server,dim,[{'x':0,'y':0,'z':0,'drill':8}],maxlevel,min,
                   {'x':float('inf'),'y':float('inf')})
    # max query
    maxp = findext(server,dim,[{'x':0,'y':0,'z':0,'drill':8}],maxlevel,max,
                   {'x':float('-inf'),'y':float('-inf')})
    
    return (num2deg(minp),num2deg(maxp))


### configs
def initConfig(name,url):
    config = {}
    config['nanocube'] = {name: {'url':url}}
    config['datasrc'] = {name:{'expr':name, 'colormap': 'Reds'}}
    config['widget'] = {}
    return config


def main():
    #parse the simple arguments
    argparser = argparse.ArgumentParser()
    argparser.add_argument('--width',type=int,default=1920)
    argparser.add_argument('--height',type=int,default=1080)
    argparser.add_argument('-s','--server',type=str)
    argparser.add_argument('-p','--port',type=int,required=False,
                           const=8000,nargs='?')
    argparser.add_argument('-w','--web',type=str,required=False,
                           default=os.environ['NANOCUBE_SRC']+'/extra/web',
                           nargs='?')

    args = argparser.parse_args()
    if args.server is None:
        sys.stderr.writelines(['Please specify a nanocube server with -s\n'])
        sys.exit(1)


    #download the schema
    url = args.server
    try:
        r = requests.get(url+'/schema', allow_redirects=False,verify=False)
        schema = r.json()
    except:
        print ('Fail to read schema from %s'%(url),file=sys.stderr)
        sys.exit(1)

    spatialvars = [ x for x in schema['fields'] if
                    x['type'].startswith('nc_dim_quadtree')]
    catvars = [ x for x in schema['fields'] if
                x['type'].startswith('nc_dim_cat')]
    timevars = [ x for x in schema['fields'] if
                 x['type'].startswith('nc_dim_time')]


    schema['metadata'] = {s['key']:s['value'] for s in schema['metadata']}
    ncname = schema['metadata']['name']
    ncname = ncname.replace(' ','_').replace('-','_').replace('.','_')
    config = initConfig(ncname, url)

    sp = {v['name']: spatialWidget(v,w=1.0/len(spatialvars)) for
          v in spatialvars}
    ts = {v['name']: timeseriesWidget(v,w=0.5*args.width) for
          v in timevars}
    cats = {v['name']: catWidget(v) for
            v in catvars}

    for c in cats:
        config['widget'][c] = cats[c]

    for s in sp:
        sp[s]['viewbox'] = findbox(url,s,sp[s]['levels']-8)
        config['widget'][s] = sp[s]

    for t in ts:
        config['widget'][t] = ts[t]

    if args.port is None:
        print(json.dumps(config,indent=2))
    else:        
        #copy the webgui to a tmp dir start a webserver
        tmpdir = tempfile.mkdtemp()
        shutil.copytree(args.web,
                        tmpdir+'/web')
        os.chdir(tmpdir+'/web')
        f = open('config.json','w')
        f.write(json.dumps(config))
        f.close()
        
        #start httpserver
        handler= http.server.SimpleHTTPRequestHandler
        httpd = http.server.HTTPServer(('', args.port),handler);

        print("See Nanocubes at http://%s:%d/"%(socket.getfqdn(),
                                                args.port))
        httpd.serve_forever()
        httpd.socket.close()
        shutil.rmtree(tmpdir)
        
def spatialWidget(v,w=1.0):
    levels = v['type'].replace('nc_dim_quadtree_','')
    levels = int(levels)
    return {
        'type':'spatial',
        'title': v['name'],
        'tilesurl':'http://{s}.tile.stamen.com/toner-lite/{z}/{x}/{y}.png',
        'coarse_offset':2,
        'viewbox':[[-85,-180],[85,180]],
        'levels':levels,
        'css':{
            'width':'%d%%'%(int(w*100)),
            'height': '100%',
            'float':'left'
        }
    }

def timeseriesWidget(v,w):
    return {
        'title': v['name'],
        'type':'time',

        'css':{
            'opacity': 0.8,
            'bottom': '30px',
            'height': '100px',
            'width': '%dpx'%(int(w)),
            'position': 'absolute',
            'background-color': '#555',
            'left': '30px'
        }
    }

def catWidget(v):
    maxkeylen = max([ len(k) for k in v['valnames'].keys() ])
    marginleft = int(maxkeylen*11)
    width = marginleft+200
    height = min(200,len(v['valnames'].keys())*20+40)

    return {
        'title':v['name'],
        'logaxis': False,
        'alpha_order': True,
        'type':'cat',        
        
        'css':{
            'opacity': 0.8,
            'height': '%dpx'%(height),
            'width': '%dpx'%(width),
        }
    }

if __name__ == '__main__':
    main()
