// buttons/IconButton4 — Material IconButton distinct v4
// unique: large — hash e0fe
export class IconButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'IconButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary iconbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}