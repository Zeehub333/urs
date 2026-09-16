// buttons/DropdownButton4 — Material DropdownButton distinct v4
// unique: large — hash a019
export class DropdownButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DropdownButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary dropdownbutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}