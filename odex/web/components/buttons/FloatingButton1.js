// buttons/FloatingButton1 — Material FloatingButton distinct v1
// unique: filled — hash 6b63
export class FloatingButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'FloatingButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary floatingbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}