/** Behavioral frontend tests without browser automation or network access. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

/** Load dashboard functions into an isolated context without bootstrapping the browser. */
function dashboard(fetchFn, env={}) {
  const store = {...(env.storage||{})};
  const elements = new Map();
  const absent = new Set(['#settings-form','.graph-area']);
  const document = {
    title: '',
    documentElement: {dataset:{},style:{}},
    querySelector(selector) {
      if (absent.has(selector)) return null;
      if (!elements.has(selector)) elements.set(selector, {textContent:'',innerHTML:'',hidden:false,title:'',content:'',classList:{add(){},remove(){},toggle(){}},querySelectorAll(){return [];},setAttribute(){},getAttribute(){return null;}});
      return elements.get(selector);
    },
    querySelectorAll() { return []; },
  };
  const matchMedia = (query)=>({matches:query.includes('dark')?!!env.darkMode:false,media:query,addEventListener(){},addListener(){},removeEventListener(){},removeListener(){}});
  const localStorage = {getItem(k){return k in store?store[k]:(k==='khaya-mode'?'live':null);},setItem(k,v){store[k]=String(v);},removeItem(k){delete store[k];}};
  const context = vm.createContext({console,document,location:{hash:'',origin:'http://127.0.0.1:8765'},window:{},localStorage,matchMedia,setTimeout,clearTimeout,AbortController,fetch:fetchFn});
  vm.runInContext(fs.readFileSync('static/sample-data.js','utf8'),context);
  const source = fs.readFileSync('static/app.js','utf8').replace(/initialize\(\);\s*$/,'');
  vm.runInContext(source+'\nglobalThis.subject={state,esc,filteredDevices,layoutGraph,renderOverview,renderDevices,renderMap,renderActivity,renderSettings,graphMarkup,refresh,setMode,openDetail,sharedAddressDevices,sharedAddressNotice,currentTheme,resolvedTheme,applyTheme,setTheme,document};',context);
  context.subject.state.snapshot = context.window.KHAYA_SAMPLE;
  context.subject.state.sample = true;
  return context.subject;
}

/** Build an API-shaped fetch response from a snapshot fixture. */
function response(snapshot) {return {ok:true,json:async()=>snapshot};}

/** Create a deferred fetch whose completion is controlled by a regression test. */
function deferredFetch() {const pending=[];return {pending,fetch:(path)=>new Promise(resolve=>pending.push({path,resolve}))};}

test('mode switch cannot display sample observations as real inventory', async()=>{
  const deferred=deferredFetch();
  const app=dashboard(deferred.fetch);
  const old=app.refresh();
  const switched=app.setMode(false);
  const real={sample:false,devices:[],events:[],counts:{online:0,offline:0,unknown:0},settings:{interval:30},collector:{running:true}};
  // Resolve the newer mode first when it exists, then the stale sample response.
  if(deferred.pending[1]) deferred.pending[1].resolve(response(real));
  deferred.pending[0].resolve(response(app.state.snapshot||{sample:true,devices:[{id:'sample',status:'online'}],events:[],counts:{online:1,offline:0,unknown:0},settings:{interval:30}}));
  await Promise.all([old,switched]);
  assert.equal(app.state.sample,false);
  assert.equal(app.state.snapshot.sample,false);
  assert.equal(app.state.snapshot.devices.length,0);
});

test('graph contains only configured edges and every device exactly once',()=>{
  const app=dashboard();
  const layout=app.layoutGraph(false);
  assert.equal(layout.positions.size,app.state.snapshot.devices.length);
  assert.equal(new Set(layout.devices.map(d=>d.id)).size,layout.devices.length);
  for(const d of layout.devices) {
    const p=layout.positions.get(d.id);
    assert.ok(Number.isFinite(p.x)&&Number.isFinite(p.y));
    if(d.parent_id) assert.ok(p.x>layout.positions.get(d.parent_id).x);
  }
  const markup=app.graphMarkup(false);
  assert.equal((markup.match(/class="graph-edge /g)||[]).length,layout.devices.filter(d=>d.parent_id).length);
});

test('overview graph retains computer ancestors and full map includes phones and IoT',()=>{
  const app=dashboard();
  assert.equal(app.layoutGraph(true).devices.filter(d=>d.type==='computer').length,3);
  assert.equal(app.layoutGraph(true).devices.filter(d=>d.type==='phone').length,0);
  assert.match(app.renderMap(),/Nest Thermostat/);
  assert.match(app.renderMap(),/iPhone 16/);
});

test('inventory filters combine type, location, status, and search',()=>{
  const app=dashboard();
  app.state.filters={search:'APPLE',type:'computer',status:'online',location:'Office'};
  assert.deepEqual(Array.from(app.filteredDevices(),d=>d.name),['MacBook Pro']);
  app.state.filters.search='nothing here';
  assert.equal(app.filteredDevices().length,0);
});

test('device HTML and SVG labels escape inventory text',()=>{
  const app=dashboard();
  const d=app.state.snapshot.devices[0];
  d.name='<script>alert(1)</script>';
  d.target='" onload="alert(1)';
  d.location='<img src=x onerror=alert(1)>';
  const html=app.renderDevices()+app.graphMarkup(false);
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&quot; onload=&quot;'));
});

test('all pages render the supplied state without undefined values',()=>{
  const app=dashboard();
  for(const page of ['renderOverview','renderDevices','renderMap','renderActivity','renderSettings']) {
    const markup=app[page]();
    assert.ok(markup.length>100);
    assert.ok(!markup.includes('undefined'));
  }
});


test('settings opened offline reload real values when the service reconnects', async()=>{
  const real={sample:true,devices:[],events:[],counts:{online:0,offline:0,unknown:0},settings:{subnet:'192.168.10.0/24',interval:50,failure_threshold:5,heartbeat_timeout:300},collector:{running:true}};
  const app=dashboard(async()=>response(real));
  app.state.page='settings';
  app.state.reachable=false;
  const original=app.document.querySelector.bind(app.document);
  app.document.querySelector=selector=>selector==='#settings-form'?{}:original(selector);
  await app.refresh();
  assert.ok(app.document.querySelector('#page-content').innerHTML.includes('192.168.10.0/24'));
  assert.ok(app.document.querySelector('#page-content').innerHTML.includes('value="50"'));
});

test('monitoring settings are disabled until the real service is connected',()=>{
  const app=dashboard();
  app.state.reachable=false;
  assert.match(app.renderSettings(), /<fieldset[^>]*disabled/);
  app.state.reachable=true;
  assert.ok(!/<fieldset[^>]*disabled/.test(app.renderSettings()));
});

test('settings offers a system-synced appearance option',()=>{
  const app=dashboard();
  const markup=app.renderSettings();
  assert.match(markup, /id="theme-select"/);
  assert.match(markup, /value="system"/);
  assert.match(markup, /value="light"/);
  assert.match(markup, /value="dark"/);
});

test('stored dark preference overrides the OS setting',()=>{
  const app=dashboard(undefined,{storage:{'khaya-theme':'dark'},darkMode:true});
  assert.equal(app.currentTheme(),'dark');
  assert.equal(app.applyTheme(),'dark');
});

test('system preference follows the OS setting',()=>{
  const light=dashboard(undefined,{storage:{},darkMode:false});
  assert.equal(light.applyTheme(),'light');
  const dark=dashboard(undefined,{storage:{},darkMode:true});
  assert.equal(dark.applyTheme(),'dark');
});


test('inventory does not label an undocumented connection as Ethernet',()=>{
  const app=dashboard();
  app.state.snapshot.devices=[{...app.state.snapshot.devices[0],connection:'unknown'}];
  const html=app.renderDevices();
  assert.ok(html.includes('Unknown connection'));
  assert.ok(!html.includes('Ethernet'));
});

/** Build an inventory with one address shared by two records. */
function sharingFixtures(app) {
  const base=app.state.snapshot.devices[0];
  app.state.snapshot.devices=[
    {...base,id:'a',name:'Office PC',target:'192.168.1.20'},
    {...base,id:'b',name:'Replacement PC',target:'192.168.1.20'},
    {...base,id:'c',name:'Printer',target:'192.168.1.30'},
  ];
}

test('inventory flags every row sharing an address, and only those rows',()=>{
  const app=dashboard();
  sharingFixtures(app);
  const markup=app.renderDevices();
  assert.equal((markup.match(/shared-address-flag/g)||[]).length,2);
  assert.match(markup,/Also used by Replacement PC/);
  assert.match(markup,/Also used by Office PC/);
});

test('device detail links to the other record sharing its address',async()=>{
  const app=dashboard();
  sharingFixtures(app);
  await app.openDetail('a');
  const shared=app.document.querySelector('#detail-content').innerHTML;
  assert.match(shared,/shared-address/);
  assert.match(shared,/Replacement PC/);
  assert.match(shared,/data-detail="b"/);
  await app.openDetail('c');
  assert.ok(!app.document.querySelector('#detail-content').innerHTML.includes('shared-address'));
});
