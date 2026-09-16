// buttons/ToggleButton1 — Material ToggleButton distinct v1
// unique: filled — hash 9a68
export class ToggleButton1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ToggleButton1'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary togglebutton1" style="padding:10px 20px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(filled)</small></button>'; }
}