// buttons/ToggleButton6 — Material ToggleButton distinct v6
// unique: loading spinner — hash 3c3c
export class ToggleButton6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'ToggleButton6'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary togglebutton6" onclick="'+onClick+'"><span style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin-right:6px"></span>'+label+' — loading spinner</button><style>@keyframes spin{to{transform:rotate(360deg)}}</style>'; }
}