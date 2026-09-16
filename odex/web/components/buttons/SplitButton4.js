// buttons/SplitButton4 — Material SplitButton distinct v4
// unique: large — hash c175
export class SplitButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SplitButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary splitbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}