import * as d3 from 'd3';

export interface User {
    id: number;
    name: string;
}

export class UserRenderer {
    render(data: User[]) {
        d3.select('#chart')
            .selectAll('div')
            .data(data)
            .enter()
            .append('div')
            .text(d => d.name);
    }
}

export function helper() {
    console.log('Helping...');
}
