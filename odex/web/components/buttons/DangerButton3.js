// buttons/DangerButton3 — Material DangerButton distinct v3
// unique: small — hash d6d5
export class DangerButton3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DangerButton3'; var onClick=p.onClick||''; return '<button class="md-btn md-btn dangerbutton3" style="padding:6px 12px;font-size:12px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(small)</small></button>'; }
}