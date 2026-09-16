// buttons/IconButton3 — Material IconButton distinct v3
// unique: small — hash 5a4d
export class IconButton3 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'IconButton3'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary iconbutton3" style="padding:6px 12px;font-size:12px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(small)</small></button>'; }
}