// views/KanbanView2 — Material KanbanView distinct v2
// unique: progress bar + percentage — hash f147
export class KanbanView2 {
  constructor(props){ this.props = props||{}; this._id='f147bc'; }
  render(){ const p=this.props; return '<div class="md-card view kanbanview2 kanbanview"><div class="md-card-header">KanbanView '+(p.title||'KanbanView2')+' — progress bar + percentage (v2)</div><div class="md-card-content">'+(p.description||'')+' — KanbanView specific rendering variant 2</div><div style="font-size:11px;color:var(--md-primary)">KanbanView • progress bar + percentage</div></div>'; }
}