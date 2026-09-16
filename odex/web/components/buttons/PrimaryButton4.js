// buttons/PrimaryButton4 — Material PrimaryButton distinct v4
// unique: large — hash ce24
export class PrimaryButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'PrimaryButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary primarybutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}