// buttons/IconButton2 — Material IconButton distinct v2
// unique: filled with icon — hash a65e
export class IconButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'IconButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary iconbutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">favorite</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}