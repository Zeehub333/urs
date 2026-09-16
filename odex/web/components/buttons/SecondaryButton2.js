// buttons/SecondaryButton2 — Material SecondaryButton distinct v2
// unique: filled with icon — hash b2d9
export class SecondaryButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SecondaryButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary secondarybutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">bookmark</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}