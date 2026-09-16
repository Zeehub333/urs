// buttons/SplitButton2 — Material SplitButton distinct v2
// unique: filled with icon — hash c01a
export class SplitButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SplitButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary splitbutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">call_split</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}