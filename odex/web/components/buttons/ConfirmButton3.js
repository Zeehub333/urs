// buttons/ConfirmButton3 — Material ConfirmButton distinct v3
// unique: small — hash d0a5
export class ConfirmButton3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ConfirmButton3'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary confirmbutton3" style="padding:6px 12px;font-size:12px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(small)</small></button>'; }
}