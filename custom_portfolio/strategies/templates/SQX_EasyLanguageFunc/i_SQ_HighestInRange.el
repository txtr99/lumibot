inputs:
	timeFrom( "08:00" ), timeTo( "16:00" ) ;

variables:  
	Output( 0 ) ;

Output = SQ_HighestInRange( timeFrom, timeTo ) ;
 
Plot1( Output, !("Highest In Range" ));
