// views/DataView3 — Material DataView distinct v3
// unique: avatar + subtitle + id — hash 4441
export class DataView3 {
  constructor(props){ this.props = props||{}; this._id='44416a'; }
  render(){ const p=this.props; return '<div class="md-card view dataview3" style="display:flex;gap:12px;align-items:center"><img src="'+(p.avatar||'https://picsum.photos/seed/DataView3/48')+'" style="width:48px;height:48px;border-radius:50%"/><div><div style="font-weight:500">'+(p.title||'DataView3')+'</div><div style="font-size:12px;color:#666">'+(p.subtitle||p.description||'')+'</div></div></div>'; }
}