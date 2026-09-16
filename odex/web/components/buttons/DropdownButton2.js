// buttons/DropdownButton2 — Material DropdownButton distinct v2
// unique: filled with icon — hash ccad
export class DropdownButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DropdownButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary dropdownbutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">arrow_drop_down</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}