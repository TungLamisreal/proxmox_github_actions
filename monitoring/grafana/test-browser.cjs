const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:920,height:940}});
  const errors = [];
  page.on('pageerror', e => { errors.push(e.message); console.log('PAGEERROR', e.message); });
  page.on('console', async msg => { if (msg.type() === 'error') {
    console.log('CONSOLE', msg.text());
    for (const arg of msg.args()) console.log('DETAIL', await arg.evaluate(v => v instanceof Error ? {message:v.message, stack:v.stack, cause:String(v.cause), causeStack:v.cause?.stack} : String(v)).catch(()=>'?'));
  }});
  page.on('requestfailed', r => console.log('REQUESTFAILED', r.url(), r.failure()));
  page.on('response', r => { if(r.status()>=400) console.log('HTTPERROR',r.status(),r.url()); });
  await page.goto('http://127.0.0.1:3000/d/meow-autoscale?from=now-15m&to=now&refresh=10s');
  const panel = page.getByTestId('meow-alertlog');
  await panel.waitFor({timeout:30000}).catch(async e => {
    console.log('PAGE TEXT', await page.locator('body').innerText());
    await page.screenshot({path:'/tmp/meow-dashboard.png',fullPage:true});
    throw e;
  });
  await page.getByText('200 dòng · tối đa 200 dòng mới nhất', {exact:true}).waitFor({timeout:30000});
  await page.getByText('backend-05',{exact:true}).waitFor({timeout:30000});
  await page.getByText('frontend-05',{exact:true}).waitFor({timeout:30000});
  const scroll = page.getByTestId('meow-log-scroll');
  assert(await scroll.evaluate(e => e.scrollHeight > e.clientHeight), 'Long log scrolls within panel');
  for (const name of ['backend-05','frontend-05']) {
    const bounds = await page.getByText(name,{exact:true}).boundingBox();
    assert(bounds.y + bounds.height < 940, name + ' outside half-screen viewport');
  }
  await scroll.evaluate(e => e.scrollTop = e.scrollHeight);
  await page.waitForTimeout(12000);
  assert.equal(await scroll.evaluate(e => e.scrollTop), 0, 'Refresh returns to newest logs');
  await page.screenshot({path:'/tmp/meow-dashboard.png',fullPage:true});
  assert(!errors.some(e => /meowcloud|PanelPlugin|useTheme2|createElement/.test(e)), errors.join('\n'));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
