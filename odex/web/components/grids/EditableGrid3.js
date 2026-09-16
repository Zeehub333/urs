// grids/EditableGrid3 — Material EditableGrid distinct v3
export class EditableGrid3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var items=p.items||[]; var cols=p.cols||3; return '<div class="md-card grid editablegrid3"><div style="font-size:11px;color:var(--md-primary);margin-bottom:8px">EditableGrid — virtual scroll</div><div style="display:grid;grid-template-columns:repeat('+cols+',1fr);gap:8px">'+items.map(i=>'<div class="md-card" style="padding:8px">'+JSON.stringify(i).slice(0,30)+'</div>').join('')+'</div></div>'; }
}