// charts/GaugeChart5 — Material GaugeChart distinct v5
// unique: horizontal bars
export class GaugeChart5 {
  constructor(props){ this.props = props||{}; }
  render(){ const p=this.props; var data=p.data||[]; var opts=p.opts||{}; var bars=data.map(function(d,i){ var w=d.y||d.value||10; var label=d.x||d.label||'Q'+(i+1); return '<div style="display:flex;align-items:center;gap:8px;margin:3px 0"><span style="width:28px;font-size:11px">'+label+'</span><div style="flex:1;height:10px;background:var(--md-background);border-radius:999px;overflow:hidden"><div style="width:'+w+'%;height:100%;background:var(--md-primary);border-radius:999px"></div></div><span style="font-size:11px">'+w+'</span></div>'; }).join(''); return '<div class="card-section chart gaugechart5"><div class="section-title">GaugeChart — horizontal bars ('+(opts.title||'GaugeChart5')+')</div><div>'+bars+'</div><small style="color:#666">'+data.length+' pts</small></div>'; }
}