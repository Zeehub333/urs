// buttons/ToggleButton4 — Material ToggleButton distinct v4
// unique: large — hash c07e
export class ToggleButton4 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ToggleButton4'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary togglebutton4" style="padding:14px 28px;font-size:16px" onclick="'+onClick+'">'+label+' <small style="opacity:.6">(large)</small></button>'; }
}