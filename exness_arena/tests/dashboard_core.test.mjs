import test from 'node:test';
import assert from 'node:assert/strict';
import {money,percent,totals,matches,sorted,pageOf,exposure,groupEvents,csv,escapeHTML,cohortKey,freshStatus} from '../dashboard/dashboard-core.mjs';

test('cent conversion, missing statistics and signed values remain distinct',()=>{
  assert.equal(money(350,'USC'),' +350.00'.trim());
  assert.equal(money(350,'USD'),'+3.5000');
  assert.equal(money(-3.5,'USD'),'-0.0350');
  assert.equal(money(null),'—');assert.equal(money(NaN),'—');assert.equal(percent(null),'—');
  assert.equal(money(0),'0.00');
});
test('portfolio reconciliation uses booked plus floating, not closed plus floating',()=>{
  const rows=[{total_net_usc:4,booked_net_usc:-1,floating_usc:5,closed_net_usc:-3,open_positions:2,closed_positions:1},{total_net_usc:-2,booked_net_usc:-4,floating_usc:2,open_positions:1,closed_positions:3}];
  const t=totals(rows);assert.equal(t.net,t.booked+t.floating);assert.equal(t.net,2);assert.equal(t.open,3);assert.equal(t.closed,4);
});
test('scope and search preserve repository and strategy identity',()=>{
  const a={repo:'tradingbot',strategy:'donchian',symbol:'US500c'};
  assert.ok(matches(a,{repo:'tradingbot',strategy:'donchian',q:'aurum'}));
  assert.ok(!matches(a,{repo:'Monn'}));assert.ok(!matches(a,{strategy:'ema_cross'}));
  assert.ok(matches(a,{q:'us500'}));
});
test('sorting is stable with unknown values and pagination clamps after filters',()=>{
  const rows=[{p:null},{p:-2},{p:0},{p:3}];assert.deepEqual(sorted(rows,'p').map(r=>r.p),[3,0,-2,null]);
  assert.deepEqual(sorted(rows,'p','asc').map(r=>r.p),[-2,0,3,null]);
  assert.equal(pageOf(rows,100,3).page,2);assert.equal(pageOf([],3).start,0);assert.equal(pageOf([],3).pages,1);
});
test('exposure counts independent buy and sell tickets without netting',()=>{
  assert.deepEqual(exposure([{symbol:'US500c',direction:'buy'},{symbol:'US500c',direction:'sell'},{symbol:'US500c',direction:'buy'}]),[{symbol:'US500c',buy:2,sell:1,total:3}]);
});
test('paper versions and cohorts cannot be mistaken for one experiment',()=>{
  const row={bot:'tradingbot',strategy:'donchian',version_id:'a',mode:'paper_seed',variant:'proposed'};
  assert.notEqual(cohortKey(row),cohortKey({...row,version_id:'b'}));
  assert.notEqual(cohortKey(row),cohortKey({...row,mode:'live_evaluation'}));
});
test('events group only identical repository, strategy, kind and detail',()=>{
  const event={repo:'Monn',strategy:'ma_cross',kind:'blocked',detail:'halt',time:'a'};
  const groups=groupEvents([event,{...event,time:'b'},{...event,strategy:'other'}]);assert.equal(groups.length,2);assert.equal(groups[0].count,2);
});
test('broker text is escaped and spreadsheet formulas are neutralized in exports',()=>{
  assert.equal(escapeHTML('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
  const text=csv([{name:'=WEBSERVICE("bad")',pnl:-3.5,note:'line\n"quoted"'}]);
  assert.ok(text.includes("'=WEBSERVICE"));assert.ok(text.includes('"-3.5"'));assert.ok(text.includes('""quoted""'));
});
test('running processes with stale heartbeats are not presented as online',()=>{
  assert.equal(freshStatus({status:'running',time:'2026-01-01T00:00:00Z'},Date.parse('2026-01-01T00:02:00Z')),'stale');
  assert.equal(freshStatus({status:'blocked'}),'blocked');
});
