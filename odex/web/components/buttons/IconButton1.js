// buttons/IconButton1 — Material IconButton distinct v1
// unique: filled — hash e2ef
export class IconButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'IconButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary iconbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}