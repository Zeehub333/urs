// buttons/FloatingButton4 — Material FloatingButton distinct v4
// unique: large — hash 9a2f
export class FloatingButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'FloatingButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary floatingbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}