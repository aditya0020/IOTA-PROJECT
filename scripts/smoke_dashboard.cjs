/* Full browser checks against a temporary Flask server; no production data used. */
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');

const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const port = 5089;
const base = `http://127.0.0.1:${port}`;
const adminKey = crypto.randomUUID(), deviceKey = crypto.randomUUID();
const db = path.join(root, 'test-results', `ui-${crypto.randomUUID()}.sqlite3`);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function post(url, body, key) {
  const response = await fetch(base + url, {method:'POST', headers:{'Content-Type':'application/json','X-API-Key':key}, body:JSON.stringify(body)});
  assert.equal(response.status, 201, await response.text());
}

async function main() {
  fs.mkdirSync(path.join(root, 'test-results'), {recursive:true});
  // A static-only server reproduces VS Code Live Server, on a separate origin.
  const preview = http.createServer((req, res) => {
    const name = req.url.replace(/^\/dashboard\//, '');
    const types = {'index.html':'text/html', 'styles.css':'text/css', 'app.js':'text/javascript'};
    if (!Object.hasOwn(types, name)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, {'Content-Type': types[name]});
    res.end(fs.readFileSync(path.join(root, 'dashboard', name)));
  });
  await new Promise(resolve => preview.listen(0, '127.0.0.1', resolve));
  const previewBase = `http://127.0.0.1:${preview.address().port}`;
  const server = spawn(python, ['-m','waitress','--listen=127.0.0.1:5089','--call','backend.app:create_app'], {
    cwd:root, env:{...process.env,DATABASE_PATH:db,ADMIN_API_KEY:adminKey,DEVICE_API_KEY:deviceKey,BLOCKCHAIN_ENABLED:'false',CORS_ALLOWED_ORIGINS:previewBase},
    windowsHide:true, stdio:['ignore','pipe','pipe']
  });
  let output = '';
  server.stdout.on('data',data=>output+=data);
  server.stderr.on('data',data=>output+=data);
  let browser;
  try {
    let ready = false;
    for (let i=0;i<40;i++) {
      if (server.exitCode !== null) throw new Error(output);
      try { ready = (await fetch(base+'/api/health')).ok; } catch {}
      if (ready) break;
      await delay(250);
    }
    assert.ok(ready,'Temporary backend did not start');
    browser = await chromium.launch({channel:process.env.BROWSER_CHANNEL || 'msedge',headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);
    await page.locator('#api-key').fill('wrong-key');
    await page.locator('#login-form button').click();
    await page.locator('#login-error').filter({hasText:'Unauthorized'}).waitFor();
    await page.locator('#api-key').fill(adminKey);
    await page.locator('#login-form button').click();
    await page.locator('#empty-packages').waitFor();
    await page.locator('#empty-register').click();
    await page.locator('[name=name]').fill('Precision instruments');
    await page.locator('[name=origin]').fill('Campus laboratory');
    await page.locator('[name=destination]').fill('Central warehouse');
    await page.locator('#register-form button[type=submit]').click();
    await page.locator('#package-name').filter({hasText:'Precision instruments'}).waitFor();
    await page.locator('#last-reading').filter({hasText:'No readings yet'}).waitFor();
    for (let i=0;i<28;i++) {
      await post('/api/readings', {package_id:'PKG001', reading_id:`browser-${i}`,seq:156+i,
        timestamp:new Date(Date.now()-(28-i)*2500).toISOString(), temperature:i===27?38:26+Math.sin(i/3)*1.5,
        humidity:61+Math.cos(i/4)*2,distance:14.2,accel_mps2:9.81,gyro_radps:.04,source_status:'NORMAL'},deviceKey);
    }
    await page.locator('#refresh').click();
    await page.locator('#package-status').filter({hasText:'WARNING'}).waitFor();
    assert.match(await page.locator('#temperature').innerText(),/38.0/);
    assert.equal(await page.locator('#history tr').count(),28);
    assert.equal(await page.locator('#chart svg').count(),1);
    await page.locator('#chart-metric').selectOption('gyro_radps');
    assert.match(await page.locator('#chart-label').innerText(),/Rotation/);
    await page.locator('#chart-metric').selectOption('temperature');
    await page.locator('#verify').click();
    await page.locator('#verification-state').filter({hasText:'OFFLINE'}).waitFor();
    await page.locator('#history-limit').selectOption('25');
    await page.waitForFunction(()=>document.querySelectorAll('#history tr').length===25);
    await page.locator('#older').click();
    await page.waitForFunction(()=>document.querySelectorAll('#history tr').length===3);
    await page.locator('#newer').click();
    await page.waitForFunction(()=>document.querySelectorAll('#history tr').length===25);
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
    await page.screenshot({path:path.join(root,'test-results/dashboard-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(root,'test-results/dashboard-mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Mobile layout overflows');
    await page.setViewportSize({width:1440,height:1100});
    // Backend failure must remain visible and must not be presented as fresh live data.
    await page.route('**/api/packages/PKG001/live',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Gateway test unavailable'})}));
    await page.locator('#refresh').click();
    await page.locator('#page-error').filter({hasText:'Gateway test unavailable'}).waitFor();
    assert.match(await page.locator('#connection').innerText(),/interrupted/);
    await page.unroute('**/api/packages/PKG001/live');
    await page.locator('#refresh').click();
    await page.locator('#page-error').waitFor({state:'hidden'});
    await page.locator('#logout').click();
    await page.locator('#access').waitFor();
    await page.goto(`${previewBase}/dashboard/index.html`);
    assert.equal(await page.locator('#access').evaluate(node => getComputedStyle(node).display), 'grid');
    await page.locator('#backend-url').fill(base);
    await page.locator('#api-key').fill(adminKey);
    await page.locator('#login-form button').click();
    await page.locator('#package-name').filter({hasText:'Precision instruments'}).waitFor();
    await page.locator('#open-register').click();
    await page.locator('[name=id]').fill('PKG002');
    await page.locator('[name=name]').fill('Live Server shipment');
    await page.locator('[name=origin]').fill('Lab');
    await page.locator('[name=destination]').fill('Warehouse');
    await page.locator('#register-form button[type=submit]').click();
    await page.locator('#package-name').filter({hasText:'Live Server shipment'}).waitFor();
    // A Live Server reload must restore the validated session and selected shipment.
    await page.reload();
    await page.locator('#package-name').filter({hasText:'Live Server shipment'}).waitFor();
    assert.ok(await page.locator('#access').isHidden());
    assert.equal(await page.locator('#backend-url').inputValue(), base);
    assert.equal(await page.evaluate(() => localStorage.length), 0);
    // No interaction across multiple background refresh cycles should sign the user out.
    await page.waitForTimeout(6500);
    assert.ok(await page.locator('#access').isHidden());
    await page.reload();
    await page.locator('#package-name').filter({hasText:'Live Server shipment'}).waitFor();
    // A cached key is not an authorization bypass: Flask must accept it again.
    await page.evaluate(() => {
      const key = 'trace.workspace.session.v1';
      const saved = JSON.parse(sessionStorage.getItem(key));
      saved.key = 'invalid-cached-key';
      sessionStorage.setItem(key, JSON.stringify(saved));
    });
    await page.reload();
    await page.locator('#login-error').filter({hasText:'Unauthorized'}).waitFor();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('trace.workspace.session.v1')), null);
    await page.locator('#backend-url').fill(base);
    await page.locator('#api-key').fill(adminKey);
    await page.locator('#login-form button').click();
    await page.locator('#workspace').waitFor();
    await page.locator('#logout').click();
    await page.reload();
    await page.locator('#access').waitFor();
    assert.ok(await page.locator('#workspace').isHidden());
    assert.equal(await page.evaluate(() => sessionStorage.getItem('trace.workspace.session.v1')), null);
    await page.locator('#backend-url').fill(base);
    await page.route('**/api/packages?*', route => route.abort());
    await page.locator('#api-key').fill(adminKey);
    await page.locator('#login-form button').click();
    await page.locator('#login-error').filter({hasText:'Cannot connect to Flask'}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Dashboard browser checks passed, including reload session restoration, idle polling, rejected cached credentials, logout persistence and Live Server integration');
  } finally {
    if (browser) await browser.close();
    server.kill();
    preview.close();
    preview.closeAllConnections();
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
