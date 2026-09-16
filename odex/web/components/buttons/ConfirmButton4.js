// buttons/ConfirmButton4 — Material ConfirmButton distinct v4
// unique: large — hash f0c6
export class ConfirmButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ConfirmButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary confirmbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}