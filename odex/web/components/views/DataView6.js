// views/DataView6 — Material DataView distinct v6
// unique: image header + title overlay — hash 5b94
export class DataView6 {
  constructor(props){ this.props = props||{}; this._id='5b9432'; }
  render(){ const p=this.props; return '<div class="md-card view dataview6" style="overflow:hidden;padding:0"><img src="'+(p.image||'https://picsum.photos/seed/DataView6/400/120')+'" style="width:100%;height:120px;object-fit:cover"/><div style="padding:12px"><div style="font-weight:500">'+(p.title||'DataView6')+'</div><div style="font-size:13px;color:#666">'+(p.description||'')+'</div></div></div>'; }
}