// views/KanbanView6 — Material KanbanView distinct v6
// unique: image header + title overlay — hash 691d
export class KanbanView6 {
  constructor(props){ this.props = props||{}; this._id='691d5b'; }
  render(){ const p=this.props; return '<div class="md-card view kanbanview6 kanbanview"><div class="md-card-header">KanbanView '+(p.title||'KanbanView6')+' — image header + title overlay (v6)</div><div class="md-card-content">'+(p.description||'')+' — KanbanView specific rendering variant 6</div><div style="font-size:11px;color:var(--md-primary)">KanbanView • image header + title overlay</div></div>'; }
}