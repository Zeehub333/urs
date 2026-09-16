// buttons/PrimaryButton2 — Material PrimaryButton distinct v2
// unique: filled with icon — hash 61c5
export class PrimaryButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'PrimaryButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary primarybutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">star</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}