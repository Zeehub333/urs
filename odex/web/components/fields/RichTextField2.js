// fields/RichTextField2 — Material RichTextField distinct v2
// unique: with leading icon — hash 42ff
export class RichTextField2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field richtextfield2 has-icon"><span class="material-symbols-outlined" style="position:absolute;left:12px;top:50%;transform:translateY(-50%)">person</span><input style="padding-left:36px" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label style="left:36px">'+(cfg.label||'RichTextField2')+' — with leading icon</label></div>'; }
}