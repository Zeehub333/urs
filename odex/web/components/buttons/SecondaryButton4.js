// buttons/SecondaryButton4 — Material SecondaryButton distinct v4
// unique: large — hash 0d40
export class SecondaryButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SecondaryButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary secondarybutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}