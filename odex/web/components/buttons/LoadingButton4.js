// buttons/LoadingButton4 — Material LoadingButton distinct v4
// unique: large — hash 62f2
export class LoadingButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'LoadingButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary loadingbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}