// buttons/LoadingButton1 — Material LoadingButton distinct v1
// unique: filled — hash 5faf
export class LoadingButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'LoadingButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary loadingbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}