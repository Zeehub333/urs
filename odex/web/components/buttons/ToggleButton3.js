// buttons/ToggleButton3 — Material ToggleButton distinct v3
// unique: small — hash 4dc3
export class ToggleButton3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ToggleButton3'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary togglebutton3" style="padding:6px 12px;font-size:12px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(small)</small></button>'; }
}