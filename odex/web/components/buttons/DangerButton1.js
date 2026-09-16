// buttons/DangerButton1 — Material DangerButton distinct v1
// unique: filled — hash b8f3
export class DangerButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DangerButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn dangerbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}