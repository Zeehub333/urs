// buttons/DropdownButton1 — Material DropdownButton distinct v1
// unique: filled — hash 2152
export class DropdownButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'DropdownButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary dropdownbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}