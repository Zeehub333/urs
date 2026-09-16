// buttons/ToggleButton2 — Material ToggleButton distinct v2
// unique: filled with icon — hash 1832
export class ToggleButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ToggleButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary togglebutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">toggle_on</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}