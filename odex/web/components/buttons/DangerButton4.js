// buttons/DangerButton4 — Material DangerButton distinct v4
// unique: large — hash 88d9
export class DangerButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DangerButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn dangerbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}