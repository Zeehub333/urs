// buttons/SplitButton1 — Material SplitButton distinct v1
// unique: filled — hash c849
export class SplitButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SplitButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary splitbutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}