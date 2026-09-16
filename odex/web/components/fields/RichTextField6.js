// fields/RichTextField6 — Material RichTextField distinct v6
// unique: floating label — hash 20f9
export class RichTextField6 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field richtextfield6"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'RichTextField6')+' — floating label</label><div style="font-size:11px;color:#666">'+(cfg.helper||'floating label')+'</div></div>'; }
}