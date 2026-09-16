// charts/RadarChart7 — Material RadarChart distinct v7
// unique: animated bars
export class RadarChart7 {
  constructor(props){ this.props = props||{}; }
  render(){ const p=this.props; var data=p.data||[]; var opts=p.opts||{}; var bars=data.map(function(d,i){ var w=d.y||d.value||10; var label=d.x||d.label||'Q'+(i+1); return '<div style="display:flex;align-items:center;gap:8px;margin:3px 0"><span style="width:28px;font-size:11px">'+label+'</span><div style="flex:1;height:10px;background:var(--md-background);border-radius:999px;overflow:hidden"><div style="width:'+w+'%;height:100%;background:var(--md-primary);border-radius:999px"></div></div><span style="font-size:11px">'+w+'</span></div>'; }).join(''); return '<div class="card-section chart radarchart7"><div class="section-title">RadarChart — animated bars ('+(opts.title||'RadarChart7')+')</div><div>'+bars+'</div><small style="color:#666">'+data.length+' pts</small></div>'; }
}