// buttons/LoadingButton2 — Material LoadingButton distinct v2
// unique: filled with icon — hash 18d4
export class LoadingButton2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'LoadingButton2'; var onClick=p.onClick||p.on_click||''; return '<button class="md-btn md-btn-primary loadingbutton2" onclick="'+onClick+'"><span class="material-symbols-outlined" style="font-size:18px;margin-right:4px">hourglass_top</span>'+label+' <span style="font-size:10px;opacity:.6">(filled with icon)</span></button>'; }
}