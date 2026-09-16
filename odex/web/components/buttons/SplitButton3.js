// buttons/SplitButton3 — Material SplitButton distinct v3
// unique: small — hash 6746
export class SplitButton3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SplitButton3'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary splitbutton3" style="padding:6px 12px;font-size:12px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(small)</small></button>'; }
}