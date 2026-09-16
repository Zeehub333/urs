// buttons/SecondaryButton1 — Material SecondaryButton distinct v1
// unique: filled — hash 87c8
export class SecondaryButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SecondaryButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary secondarybutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}