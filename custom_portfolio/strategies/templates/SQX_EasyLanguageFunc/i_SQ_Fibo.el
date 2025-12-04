inputs:
	FiboRange( 1 ) [DisplayName = "Range", ToolTip = 
	 "High-Low previous day=1,High-low previous week=2,High-low previous month=3,Open-Close previous day=5,Open-Close previous week=6,Open-Close previous month=7"],
	FiboLevel( 38.2 ) [DisplayName = "Length", ToolTip = 
	 "-423.6=-423.6,-261.8=-261.8,-161.8=-161.8,-100.0=-100.0,-76.4=-76.4,-61.8=-61.8,-50.0=-50.0,-38.2=-38.2,-23.6=-23.6,0.0=0.0,23.6=23.6,38.2=38.2,50.0=50.0,61.8=61.8,76.4=76.4,100.0=100.0,161.8=161.8,261.8=261.8,423.6=423.6"];
;

variables:  
	FiboValue( 0 ) ;

FiboValue = SQ_Fibo( FiboRange, FiboLevel ) ;
 
Plot1( FiboValue, !("Fibo" ));
