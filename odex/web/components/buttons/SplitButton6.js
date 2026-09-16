// buttons/SplitButton6 — Material SplitButton distinct v6
// unique: loading spinner — hash 0773
export class SplitButton6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var label=p.label||'SplitButton6'; var onClick=p.onClick||''; return '<button class="md-btn md-btn-primary splitbutton6" onclick="'+onClick+'"><span style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin-right:6px"></span>'+label+' — loading spinner</button><style>@keyframes spin{to{transform:rotate(360deg)}}</style>'; }
}