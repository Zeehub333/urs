// buttons/ConfirmButton2 — Material ConfirmButton distinct v2
// unique: filled with icon — hash d1b4
export class ConfirmButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ConfirmButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary confirmbutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">check</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}