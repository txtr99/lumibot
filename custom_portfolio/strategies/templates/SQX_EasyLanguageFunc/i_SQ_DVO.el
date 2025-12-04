
inputs:
	MAPeriod( 12 ), 
	PercentRankPeriod( 120 ) ;

variables:  
	DVO( 0 ) ;

DVO = SQ_DVO( MAPeriod, PercentRankPeriod ) ;
 
Plot1( DVO, !("DVO" ));
