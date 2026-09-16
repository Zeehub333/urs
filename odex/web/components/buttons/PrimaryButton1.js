// buttons/PrimaryButton1 — Material PrimaryButton distinct v1
// unique: filled — hash c55f
export class PrimaryButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'PrimaryButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary primarybutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}