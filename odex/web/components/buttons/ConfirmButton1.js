// buttons/ConfirmButton1 — Material ConfirmButton distinct v1
// unique: filled — hash 09ae
export class ConfirmButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ConfirmButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary confirmbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}