// fields/RichTextField1 — Material RichTextField distinct v1
// unique: basic — hash 06c5
export class RichTextField1 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var v=p.value||''; var cfg=p.cfg||{}; return '<div class="md-text-field field richtextfield1"><input type="'+(cfg.type||'text')+'" value="'+v+'" placeholder="'+(cfg.placeholder||'')+'" /><label>'+(cfg.label||'RichTextField1')+' — basic</label><div style="font-size:11px;color:#666">'+(cfg.helper||'basic')+'</div></div>'; }
}