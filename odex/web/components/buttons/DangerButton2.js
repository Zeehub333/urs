// buttons/DangerButton2 — Material DangerButton distinct v2
// unique: filled with icon — hash af48
export class DangerButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DangerButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary dangerbutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">warning</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}