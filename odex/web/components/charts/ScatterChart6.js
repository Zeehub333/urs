// charts/ScatterChart6 — Material ScatterChart distinct v6
// unique: colored bars
export class ScatterChart6 {
  constructor(props){ this.props = props||{}; }
  render(){ const p=this.props; var data=p.data||[]; var opts=p.opts||{}; var bars=data.map(function(d,i){ var w=d.y||d.value||10; var label=d.x||d.label||'Q'+(i+1); return '<div style="display:flex;align-items:center;gap:8px;margin:3px 0"><span style="width:28px;font-size:11px">'+label+'</span><div style="flex:1;height:10px;background:var(--md-background);border-radius:999px;overflow:hidden"><div style="width:'+w+'%;height:100%;background:var(--md-secondary);border-radius:999px"></div></div><span style="font-size:11px">'+w+'</span></div>'; }).join(''); return '<div class="card-section chart scatterchart6"><div class="section-title">ScatterChart — colored bars ('+(opts.title||'ScatterChart6')+')</div><div>'+bars+'</div><small style="color:#666">'+data.length+' pts</small></div>'; }
}