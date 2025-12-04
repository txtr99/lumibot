inputs:
	timeFrom( "08:00" ), timeTo( "16:00" ) ;

variables:  
	Output( 0 ) ;

Output = SQ_LowestInRange( timeFrom, timeTo ) ;
 
Plot1( Output, !("Lowest In Range" ));
